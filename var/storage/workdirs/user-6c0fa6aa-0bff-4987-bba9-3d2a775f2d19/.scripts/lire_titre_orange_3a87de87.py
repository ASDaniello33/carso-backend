from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
px = im.load()

def est_orange(p):
    r, g, b = p
    return r > 190 and 110 < g < 220 and b < 140

# titre : y 410..535, x 260..1200
X0, X1, Y0, Y1 = 260, 1200, 410, 535
ech_x = 3
ech_y = 4
print('=== TITRE ORANGE (x %d..%d) ===' % (X0, X1))
for y in range(Y0, Y1, ech_y):
    ligne = ''
    for x in range(X0, X1, ech_x):
        ligne += '#' if est_orange(px[x, y]) else '.'
    print(ligne)
