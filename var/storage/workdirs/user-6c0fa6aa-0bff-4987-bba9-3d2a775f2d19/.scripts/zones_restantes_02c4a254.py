from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

print('=== COLONNE DROITE x 1808..2044 : profil vertical ===')
prec = None
for y in range(600, 2048, 8):
    p = px[1900, y]
    c = '#%02X%02X%02X' % p
    if c != prec:
        print('y', y, c, 'lum', round(lum(p)))
        prec = c

print()
print('=== BANDEAU BAS : texte sombre sur orange, y 1970..2010 ===')
for y in range(1968, 2012, 3):
    ligne = ''
    for x in range(0, 1300, 8):
        ligne += '#' if lum(px[x, y]) < 110 else '.'
    print('%4d %s' % (y, ligne))

print()
print('=== ZONE PHOTO HAUTE y 100..410 : echantillons ===')
for y in range(100, 420, 20):
    ligne = []
    for x in range(0, w, 128):
        p = px[x, y]
        ligne.append('#%02X%02X%02X' % p)
    print(y, ' '.join(ligne))
