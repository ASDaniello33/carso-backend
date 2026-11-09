from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
px = im.load()

def est_orange(p):
    r, g, b = p
    return r > 190 and 110 < g < 220 and b < 140

# colonnes occupees sur toute la hauteur du titre
Y0, Y1 = 410, 535
cols = []
for x in range(250, 1250):
    n = sum(1 for y in range(Y0, Y1) if est_orange(px[x, y]))
    cols.append((x, n))

# groupes de colonnes non vides
groupes = []
d = None
for x, n in cols:
    if n > 0 and d is None:
        d = x
    elif n == 0 and d is not None:
        groupes.append((d, x - 1))
        d = None
if d is not None:
    groupes.append((d, 1249))

# fusionner les groupes tres proches (< 12 px) = parties d'une meme lettre
fusion = []
for g in groupes:
    if fusion and g[0] - fusion[-1][1] < 14:
        fusion[-1] = (fusion[-1][0], g[1])
    else:
        fusion.append(list(g))
        fusion[-1] = tuple(fusion[-1])
fusion = [tuple(f) for f in fusion]
print('lettres detectees:', len(fusion))
for i, (x0, x1) in enumerate(fusion):
    print('lettre', i, 'x', x0, '->', x1, 'largeur', x1 - x0 + 1)

print()
for i, (x0, x1) in enumerate(fusion):
    print('=== LETTRE', i, '===')
    for y in range(Y0, Y1, 3):
        ligne = ''
        for x in range(x0, x1 + 1, 2):
            ligne += '#' if est_orange(px[x, y]) else '.'
        print(ligne)
    print()
