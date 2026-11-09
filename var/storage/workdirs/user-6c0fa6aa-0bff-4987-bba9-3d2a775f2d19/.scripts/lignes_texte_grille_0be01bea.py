from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

X0, X1 = 212, 2044
Y0, Y1 = 600, 2044

# lignes de texte : pixels clairs
print('=== LIGNES DE TEXTE (y, nb pixels clairs, x min, x max) ===')
lignes = []
for y in range(Y0, Y1):
    cols = [x for x in range(X0, X1, 2) if lum(px[x, y]) > 165]
    if len(cols) > 8:
        lignes.append((y, len(cols), min(cols), max(cols)))

# regrouper en bandes
bandes = []
if lignes:
    d = lignes[0][0]; prec = lignes[0][0]
    acc = [lignes[0]]
    for l in lignes[1:]:
        if l[0] - prec > 6:
            bandes.append((d, prec, acc)); d = l[0]; acc = []
        acc.append(l); prec = l[0]
    bandes.append((d, prec, acc))

for b in bandes:
    y0, y1, acc = b
    xmin = min(a[2] for a in acc); xmax = max(a[3] for a in acc)
    print('bande y', y0, '->', y1, 'hauteur', y1-y0, 'x', xmin, '->', xmax)

print()
print('=== CLUSTERS X PAR BANDE (colonnes) ===')
for b in bandes:
    y0, y1, acc = b
    if y1 - y0 < 8:
        continue
    # union des colonnes claires sur la bande
    cols = set()
    for y in range(y0, y1 + 1, 2):
        for x in range(X0, X1, 2):
            if lum(px[x, y]) > 165:
                cols.add(x)
    cols = sorted(cols)
    groupes = []
    if cols:
        d = cols[0]; prec = cols[0]
        for x in cols[1:]:
            if x - prec > 40:
                groupes.append((d, prec)); d = x
            prec = x
        groupes.append((d, prec))
    print('y', y0, '->', y1, ':', groupes)
