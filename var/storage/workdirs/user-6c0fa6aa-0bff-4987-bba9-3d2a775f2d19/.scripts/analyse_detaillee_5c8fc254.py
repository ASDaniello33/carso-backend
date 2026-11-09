from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def lum(p):
    return 0.299*p[0] + 0.587*p[1] + 0.114*p[2]

def est_peau(p):
    r, g, b = p
    return r > 95 and g > 40 and b > 20 and r > g and r > b and (max(r,g,b) - min(r,g,b)) > 15 and abs(r-g) > 15

# 1) BANDE BLANCHE HAUTE : texte sombre sur fond clair
print('=== BANDE HAUTE (y 0-140) : colonnes avec encre sombre ===')
for y in range(0, 140, 10):
    seg = []
    for x in range(0, w, 4):
        if lum(px[x, y]) < 120:
            seg.append(x)
    if seg:
        print('y', y, 'encre de x', min(seg), 'a', max(seg), 'nb', len(seg))

# 2) TITRE ORANGE : segmentation des glyphes
print('=== TITRE ORANGE : glyphes ===')
def est_orange(p):
    r, g, b = p
    return r > 200 and 130 < g < 210 and b < 120

bandes = []
for y in range(380, 560):
    cols = [x for x in range(0, w, 2) if est_orange(px[x, y])]
    if cols:
        bandes.append((y, min(cols), max(cols), len(cols)))
if bandes:
    print('titre y de', bandes[0][0], 'a', bandes[-1][0])
    print('x de', min(b[1] for b in bandes), 'a', max(b[2] for b in bandes))

# colonnes oranger sur la ligne mediane du titre
ymid = (bandes[0][0] + bandes[-1][0]) // 2 if bandes else 470
profil = [1 if est_orange(px[x, ymid]) else 0 for x in range(w)]
groupes = []
debut = None
for x, v in enumerate(profil):
    if v and debut is None:
        debut = x
    elif not v and debut is not None:
        if x - debut > 6:
            groupes.append((debut, x - 1, x - debut))
        debut = None
print('glyphes ligne y=', ymid, ':', len(groupes))
for g in groupes:
    print('  x', g[0], '->', g[1], 'largeur', g[2])

# 3) ZONES DE PEAU (photos)
print('=== ZONES PEAU (photos) ===')
ech = 8
W, H = w // ech, h // ech
masque = [[est_peau(px[i*ech, j*ech]) for i in range(W)] for j in range(H)]
vus = [[False]*W for _ in range(H)]
comps = []
for j in range(H):
    for i in range(W):
        if masque[j][i] and not vus[j][i]:
            pile = [(i, j)]; vus[j][i] = True
            minx = maxx = i; miny = maxy = j; n = 0
            while pile:
                x, y = pile.pop(); n += 1
                minx = min(minx, x); maxx = max(maxx, x)
                miny = min(miny, y); maxy = max(maxy, y)
                for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx, ny = x+dx, y+dy
                    if 0 <= nx < W and 0 <= ny < H and masque[ny][nx] and not vus[ny][nx]:
                        vus[ny][nx] = True; pile.append((nx, ny))
            if n > 60:
                comps.append((n, minx*ech, miny*ech, maxx*ech, maxy*ech))
comps.sort(key=lambda c: (c[2], c[1]))
for c in comps[:25]:
    n, x0, y0, x1, y1 = c
    print('peau aire', n, 'x', x0, '->', x1, 'y', y0, '->', y1, 'w', x1-x0, 'h', y1-y0)
print('nb zones peau', len(comps))
