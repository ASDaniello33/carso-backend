from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

# bande blanche haute : hauteur exacte
print('=== BANDE BLANCHE HAUTE ===')
for y in range(0, 260, 4):
    n = sum(1 for x in range(0, w, 8) if lum(px[x, y]) > 230)
    tot = len(range(0, w, 8))
    if n / tot < 0.5:
        print('fin de la bande blanche vers y =', y, '(part claire', round(100*n/tot), '%)')
        break

# bandeau orange bas : bornes
print('=== BANDEAU ORANGE BAS ===')
def est_orange(p):
    r, g, b = p
    return r > 200 and 120 < g < 215 and b < 130
ys = [y for y in range(1800, h, 2) if sum(1 for x in range(0, w, 16) if est_orange(px[x, y])) > 20]
if ys:
    print('orange de y', min(ys), 'a', max(ys))
    ymid = (min(ys) + max(ys)) // 2
    xs = [x for x in range(0, w, 2) if est_orange(px[x, ymid])]
    print('orange de x', min(xs), 'a', max(xs))

# carte : bornes precises
print('=== CARTE ===')
def est_carte(p):
    r, g, b = p
    return abs(r-0x3B) < 22 and abs(g-0x43) < 22 and abs(b-0x82) < 26
ys = [y for y in range(400, 1900, 2) if sum(1 for x in range(300, 1900, 16) if est_carte(px[x, y])) > 60]
xs = [x for x in range(0, w, 2) if sum(1 for y in range(700, 1800, 16) if est_carte(px[x, y])) > 40]
if ys: print('carte y', min(ys), '->', max(ys))
if xs: print('carte x', min(xs), '->', max(xs))

# fond general
print('=== FOND ===')
print('couleur fond (x=100,y=1000):', '#%02X%02X%02X' % px[100, 1000])
print('couleur carte (x=1000,y=1000):', '#%02X%02X%02X' % px[1000, 1000])
print('couleur titre (x=300,y=470):', '#%02X%02X%02X' % px[300, 470])
print('couleur bandeau bas (x=600,y=1980):', '#%02X%02X%02X' % px[600, 1980])
