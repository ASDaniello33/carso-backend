from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
px = im.load()

def est_orange(p):
    r, g, b = p
    return r > 190 and 110 < g < 220 and b < 140

Y0, Y1 = 410, 540
for (x0, x1, nom) in [(620, 860, 'GROUPE 3'), (855, 1200, 'GROUPE 4')]:
    print('===', nom, 'x', x0, '->', x1, '===')
    for y in range(Y0, Y1, 2):
        ligne = ''
        for x in range(x0, x1, 2):
            ligne += '#' if est_orange(px[x, y]) else '.'
        print(ligne)
    print()
