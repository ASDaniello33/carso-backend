from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

def rendre(x0, x1, y0, y1, pas_x, pas_y, seuil, titre):
    print('===', titre, '===')
    for y in range(y0, y1, pas_y):
        ligne = ''
        for x in range(x0, x1, pas_x):
            ligne += '#' if lum(px[x, y]) < seuil else '.'
        print(ligne)
    print()

# 1) petit titre orange : texte sombre ? non, orange sur fond sombre -> inverser
print('=== TITRE ORANGE y725-770 (orange = #) ===')
def est_orange(p):
    r, g, b = p
    return r > 180 and 100 < g < 225 and b < 150
for y in range(720, 775, 2):
    ligne = ''
    for x in range(260, 520, 2):
        ligne += '#' if est_orange(px[x, y]) else '.'
    print(ligne)
print()

# 2) bloc blanc : texte sombre sur blanc
rendre(256, 1340, 555, 670, 3, 3, 120, 'BLOC BLANC (texte sombre)')

# 3) premiere ligne de la carte
rendre(340, 1210, 885, 916, 3, 2, 150, 'CARTE LIGNE 1 (texte clair)')
