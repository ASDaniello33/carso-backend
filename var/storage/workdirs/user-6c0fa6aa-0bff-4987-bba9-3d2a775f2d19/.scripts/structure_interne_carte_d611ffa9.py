from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

bandes = [(889,912),(947,970),(1004,1027),(1062,1085),(1119,1142),(1176,1200),
          (1234,1257),(1291,1315),(1348,1372),(1406,1430),(1463,1487),(1521,1544),
          (1578,1602),(1636,1659),(1791,1814)]

print('=== CLUSTERS X PAR LIGNE (gap 25) ===')
for (y0, y1) in bandes:
    cols = set()
    for y in range(y0, y1+1):
        for x in range(212, 2044):
            if lum(px[x, y]) > 160:
                cols.add(x)
    cols = sorted(cols)
    groupes = []
    if cols:
        d = cols[0]; prec = cols[0]
        for x in cols[1:]:
            if x - prec > 25:
                groupes.append((d, prec)); d = x
            prec = x
        groupes.append((d, prec))
    print('y', y0, '->', y1, ':', groupes)

print()
print('=== HAUTEUR DE GLYPHE (taille de police approx) ===')
for (y0, y1) in bandes[:4]:
    print('bande', y0, y1, 'hauteur', y1-y0)

print()
print('=== ZONE HAUTE : carte ASCII fine (y 0..700) ===')
N = 80
for j in range(0, 700, 14):
    ligne = ''
    for i in range(N):
        x = int((i + 0.5) * w / N)
        p = px[x, j]
        l = lum(p)
        r, g, b = p
        if r > 200 and 120 < g < 215 and b < 130:
            ligne += 'O'
        elif l > 225:
            ligne += 'W'
        elif l > 150:
            ligne += 'w'
        elif l < 60:
            ligne += '#'
        else:
            ligne += '.'
    print('%4d ' % j + ligne)
