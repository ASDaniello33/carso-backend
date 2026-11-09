from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

print('=== COULEUR PAR BANDE HORIZONTALE (centre x=1024) ===')
prec = None
for y in range(0, h, 4):
    p = px[1024, y]
    c = '#%02X%02X%02X' % p
    if c != prec:
        print('y', y, c, 'lum', round(lum(p)))
        prec = c

print()
print('=== COULEUR PAR COLONNE (y=1500) ===')
prec = None
for x in range(0, w, 8):
    p = px[x, 1500]
    c = '#%02X%02X%02X' % p
    if c != prec:
        print('x', x, c)
        prec = c

print()
print('=== COULEUR PAR COLONNE (y=1980) ===')
prec = None
for x in range(0, w, 8):
    p = px[x, 1980]
    c = '#%02X%02X%02X' % p
    if c != prec:
        print('x', x, c)
        prec = c

print()
print('=== COULEUR PAR COLONNE (y=120) ===')
prec = None
for x in range(0, w, 8):
    p = px[x, 120]
    c = '#%02X%02X%02X' % p
    if c != prec:
        print('x', x, c)
        prec = c
